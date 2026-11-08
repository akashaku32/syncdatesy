# Salesforce — JWT Bearer integration setup

Use this guide to connect **Salesforce** or **Salesforce Sandbox** to Datasyncher using **JWT Bearer (Integration User)** authentication. This is the recommended method for unattended, server-to-server sync (batch jobs, scheduled pipelines) because it does not require browser OAuth or stored refresh tokens.

**Related**

| Auth method | When to use |
|-------------|-------------|
| **OAuth (User Authorization)** | Interactive setup, user-driven access, first-time browser login |
| **JWT Bearer (Integration User)** (this doc) | Production pipelines, batch cluster, no browser redirect |

---

## Overview

JWT Bearer flow lets Datasyncher authenticate as a dedicated Salesforce **integration user** using a **Connected App** and an RSA **private key**. Datasyncher signs a short-lived JWT assertion, exchanges it for a Salesforce access token, and uses that token for API calls (Bulk API, SOQL, etc.).

```text
Integration user + Connected App (Salesforce)
        ↓
Private key stored in Datasyncher connection config
        ↓
Signed JWT assertion → Salesforce token endpoint
        ↓
access_token + instance_url → Redis cache (~90 min) → sync jobs
```

Implementation: `backend/migration-function/utils/salesforce_jwt_auth.py`

---

## OAuth vs JWT Bearer

| | OAuth | JWT Bearer |
|---|--------|------------|
| User interaction | Browser login required | None — fully automated |
| Stored secrets | `CLIENT_SECRET`, `refresh_token`, `access_token` | `private_key`, `integration_username`, `CLIENT_ID` |
| Token refresh | Refresh token rotation | New JWT assertion when cache expires |
| Best for | Ad-hoc / user-driven access | Scheduled syncs, Kubernetes batch workers |

When `authentication_type` is `jwt_bearer`, the UI hides `CLIENT_SECRET` and OAuth redirect is skipped during connection validation.

---

## Part 1 — Salesforce administrator setup

### 1. Create an integration user

1. In Salesforce **Setup**, create a dedicated user (e.g. `integration@yourcompany.com`).
2. Ensure the user has:
   - **API Enabled** (user detail or profile)
   - Object and field permissions required for your sync
   - A Profile or Permission Set with appropriate data access

This username becomes the JWT `sub` (subject) claim and must match **exactly** what you enter in Datasyncher as **Integration Username**.

### 2. Generate an RSA key pair

You need a **private key** (stored in Datasyncher) and a matching **X.509 certificate** (uploaded to Salesforce).

**Option A — Datasyncher UI (recommended)**

1. In the connection form, select **JWT Bearer (Integration User)**.
2. Click **Generate Keys**.
3. Copy or download the **X.509 Certificate** and upload it to your Connected App under **Use digital signatures**.
4. The **private key** is saved in Datasyncher when you save the connection.

**Option B — OpenSSL (manual)**

```bash
# Generate 2048-bit private key
openssl genrsa -out jwt_private.key 2048

# Generate self-signed certificate (adjust -days as needed)
openssl req -new -x509 -key jwt_private.key -out jwt_certificate.crt -days 365
```

| File | Where it goes |
|------|----------------|
| `jwt_private.key` | Datasyncher connection — **Private Key (PEM format)** |
| `jwt_certificate.crt` | Salesforce Connected App — **Use digital signatures** upload |

> **Security:** Never commit private keys to git. Store them in connection config or secrets management only.

Example key pair location in this repo (local dev only): `spectra-sf-jwt/`

### 3. Create or configure a Connected App

**Setup → App Manager → New Connected App** (or configure an External Client App):

1. Enable **OAuth Settings**
2. Enable **Use digital signatures** and upload `jwt_certificate.crt`
3. Add OAuth scopes:
   - **Manage user data via APIs (`api`)**
   - **Perform requests at any time (`refresh_token`, `offline_access`)**
4. Set **Permitted Users** to **Admin approved users are pre-authorized**
5. Copy the **Consumer Key** — this is `CLIENT_ID` in Datasyncher

### 4. Pre-authorize the integration user

Under the Connected App **Manage** → **Profiles** or **Permission Sets**:

- Assign the integration user's Profile, **or**
- Assign a Permission Set that includes this Connected App

Without pre-authorization, token exchange fails with errors such as *user hasn't approved* or *preauthorized*.

---

## Part 2 — Datasyncher connection configuration

In the UI, add **Salesforce** (production) or **Salesforce Sandbox** as source or destination.

| Field | Required | Notes |
|-------|----------|--------|
| **Authentication Type** | Yes | Select **JWT Bearer (Integration User)** |
| **Consumer Key (Client ID)** | Yes | Connected App Consumer Key |
| **Integration Username** | Yes | Exact Salesforce username (e.g. `integration@yourcompany.com`) |
| **Private Key (PEM format)** | Yes | Full PEM contents of the private key file (or use **Generate Keys**) |
| **Private Key Passphrase** | No | Only if the private key is encrypted |
| **X.509 Certificate** | No (UI only) | Generated in UI for Connected App upload; not stored in Datasyncher |
| **Custom Domain** | No | My Domain prefix if your org uses a custom login domain |

**Not used for JWT:**

- `CLIENT_SECRET`
- Browser OAuth redirect
- `refresh_token` (stored OAuth tokens are stripped when auth type is JWT)

On edit, if the private key field is left blank or masked (`********`), Datasyncher preserves the previously stored key.

---

## Part 3 — Validation and runtime behavior

### Connection validation (Test Connection / create flow)

1. UI sends credentials to the Salesforce authorization API.
2. Backend builds an RS256-signed JWT with:
   - `iss` = Consumer Key (`CLIENT_ID`)
   - `sub` = integration username
   - `aud` = Salesforce token endpoint URL (see below)
   - `exp` = current time + 180 seconds
3. POST to token URL: `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer` + `assertion`
4. Salesforce returns `access_token` and `instance_url`.
5. Backend opens a `simple_salesforce` session and calls `describe()` to verify the connection.
6. Success — no OAuth browser redirect.

### Pipeline / batch runtime

1. `salesforce_connection()` detects `authentication_type: jwt_bearer`.
2. `get_salesforce_jwt_access_token()` checks Redis cache (`sf_jwt_access:{connection_id}:{role}`).
3. On cache miss, a new JWT assertion is exchanged for an access token.
4. Access tokens are cached for **90 minutes** (Salesforce tokens typically last ~2 hours).
5. On connection failure, cache is invalidated and one retry is attempted.

**Redis:** Batch workers need `REDIS_URL` configured (see `k8s/batch-cluster/ensure-batch-redis.sh`).

---

## Token endpoint URLs (`aud` claim)

The JWT audience must match the org type and domain.

| Org type | Default token URL |
|----------|-------------------|
| **Production** | `https://login.salesforce.com/services/oauth2/token` |
| **Sandbox** | `https://test.salesforce.com/services/oauth2/token` |

With **Custom Domain** set, Datasyncher resolves the token URL from the domain (e.g. `https://yourdomain.my.salesforce.com/services/oauth2/token` or sandbox equivalent).

Use the **Salesforce** connector for production orgs and **Salesforce Sandbox** for sandbox orgs. A mismatch (prod credentials against sandbox connector, or vice versa) causes **invalid assertion** errors.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| **invalid assertion** | Wrong key/cert pair, wrong `CLIENT_ID`, wrong username, or prod/sandbox mismatch | Verify Consumer Key, private key matches uploaded certificate, exact integration username, correct connector type |
| **invalid_client** | `CLIENT_ID` does not match Connected App | Copy Consumer Key again from the same org's Connected App |
| **user hasn't approved** / **preauthorized** | User not pre-authorized for Connected App | Set Permitted Users to admin-approved; assign Profile or Permission Set |
| **refresh_token scope is required** | Missing OAuth scopes on Connected App | Add `api`, `refresh_token`, and `offline_access` scopes |
| Connection works in UI but fails in batch | Redis unavailable or wrong `REDIS_URL` | Ensure Redis is running in the batch cluster |

Error messages from the backend include admin hints when possible (`format_salesforce_jwt_exchange_error` in `salesforce_jwt_auth.py`).

---

## Manual token exchange (debugging)

Optional curl test after generating a JWT assertion (replace placeholders):

```bash
curl -X POST "https://login.salesforce.com/services/oauth2/token" \
  -d "grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer" \
  -d "assertion=YOUR_SIGNED_JWT"
```

For sandbox, use `https://test.salesforce.com/services/oauth2/token` or your sandbox My Domain token URL.

---

## Code references

| Component | Path |
|-----------|------|
| JWT auth (assertion, exchange, cache) | `backend/migration-function/utils/salesforce_jwt_auth.py` |
| Salesforce client (JWT branch) | `backend/migration-function/services/salesforce/client.py` |
| Connection validation | `backend/migration-function/services/salesforce/validation.py` |
| Django config sanitization | `apps/elttool/v1/views.py` (`sanitize_salesforce_config_for_auth_type`, `_merge_salesforce_jwt_fields_on_edit`) |
| UI form fields | `frontend/data-syncher-ui/src/components/dashboard/helpers/DynamicForm.tsx` |
| Source attribute definitions | `apps/elttool/management/commands/populate_source_attributes.py` |
| Tests | `backend/migration-function/tests/test_salesforce_jwt_auth.py`, `apps/elttool/tests/test_salesforce_jwt_config.py` |

---

## Checklist

**Salesforce**

- [ ] Integration user created with API access and required object permissions
- [ ] RSA private key + X.509 certificate generated
- [ ] Connected App: digital signatures enabled, certificate uploaded
- [ ] OAuth scopes: `api`, `refresh_token`, `offline_access`
- [ ] Permitted Users = Admin approved users are pre-authorized
- [ ] Integration user assigned via Profile or Permission Set

**Datasyncher**

- [ ] Connector matches org type (Salesforce vs Salesforce Sandbox)
- [ ] Authentication Type = JWT Bearer (Integration User)
- [ ] Consumer Key, Integration Username, and Private Key PEM configured
- [ ] Test Connection succeeds without OAuth popup
- [ ] Batch cluster has Redis for token caching (production pipelines)
