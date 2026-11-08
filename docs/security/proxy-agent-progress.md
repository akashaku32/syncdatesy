# Proxy agent progress API — security measures

**Status:** Done on batch (Django ingest + CA mount + HTTPS gateway as **fallback**). Primary agent progress is **gRPC `ReportProgress`** on the existing hub channel (hub relay TBD outside monorepo).

**Transport default:** `grpc_with_https_fallback` — try gRPC first, then HTTPS + mTLS + HMAC.

**Ops / deploy:** [`syncdatesy-adapter/docs/operations/proxy-agent/`](../../syncdatesy-adapter/docs/operations/proxy-agent/) · Agent README: [README-AGENT.md](../../syncdatesy-adapter/docs/operations/proxy-agent/README-AGENT.md)

## Why a dedicated host

Agents and browsers are different clients. Browsers use cookies/login; agents use client certificates.

| Approach | When to use |
|----------|-------------|
| **`agent-api.<domain>` (preferred)** | Clean mTLS boundary; UI/API on `gcp.datasyncher.com` untouched |
| Path on UI host | Only if DNS for a second name is impossible (harder nginx auth-tls scoping) |
| Same host, HMAC only | Weaker — URL theft alone is closer to enough |

Dedicated host lets ingress require **client certificates for agents only** without locking humans out of the SPA.

Batch example:

- UI / API: `https://gcp.datasyncher.com`
- Agent progress: `https://agent-api.gcp.datasyncher.com/v1/migration-progress`

DNS: A record `agent-api.gcp` → same ingress LB IP as `gcp.datasyncher.com` (GoDaddy for `datasyncher.com`).

## Transport (primary vs fallback)

| Path | When | Agent auth |
|------|------|------------|
| **gRPC `ReportProgress`** | Primary (`grpc` / `grpc_with_https_fallback`) | Existing hub mTLS; same `server_host:server_port` as loads |
| **HTTPS POST** | Fallback or `https` mode | mTLS at `agent-api.*` ingress + HMAC on body |

Config still includes `progress_api_url` and `progress_signing_secret` so HTTPS fallback works without re-enrollment when gRPC progress fails.

## Defense in depth (HTTPS fallback path)

```text
On-prem agent
    │  TLS + client cert (mTLS)
    ▼
agent-api ingress  ── verifies cert vs Proxy Agent CA; CN = agent_uid
    │  forwards ssl-client-cert (PEM)
    ▼
proxy-agent-gateway  ── rate limit; strips forged X-INTERNAL-KEY / X-Agent-UID;
    │                     sets X-Agent-UID from cert CN only
    │  X-INTERNAL-KEY + X-Agent-UID + HMAC headers + body
    ▼
Django internal ingest  ── IsInternalService + HMAC + tenant/job bind
    ▼
PostgreSQL + WebSocket push
```

| Layer | Mechanism | Answers |
|-------|-----------|---------|
| 1 Transport | **mTLS** at ingress | Is this an enrolled agent? |
| 2 Edge | Gateway strips client internal headers; UID from cert only | Can a caller spoof another agent? |
| 3 Service | **`X-INTERNAL-KEY`** (`IsInternalService`) | Is this the gateway (or hub), not the public internet? |
| 4 Message | **HMAC** on body + timestamp | Was this exact payload forged or replayed? |
| 5 Tenancy | Connection / records bound to agent `cmp_id` | Cross-company updates? |

**`INTERNAL_API_KEY` must never appear in agent `config.json`.**

## What is mTLS?

Normal HTTPS is **one-way TLS**: only the server proves its identity (e.g. Let’s Encrypt).

**mTLS (mutual TLS)** adds client proof:

1. Server presents `agent-api.<domain>` certificate.
2. Agent presents the **client certificate** issued at `POST /api/v1/proxy-agent/generate/` (CN = `agent_uid`).
3. Ingress checks the cert against Secret `proxy-agent-client-ca` (Proxy Agent CA).

Invalid / missing / wrong-CA cert → connection rejected before app logic.

## What is HMAC?

**HMAC** signs each progress request with a shared secret (`progress_signing_secret` in `config.json`).

Typical headers:

- `X-Agent-Timestamp` — Unix seconds (reject if skew &gt; ~300s)
- `X-Agent-Signature` — `HMAC-SHA256(secret, timestamp + "." + raw_body)` hex

Django stores SHA-256 hash + AES-GCM encrypted secret on `ProxyAgent`; verifies with `hmac.compare_digest`. Fail closed on mismatch.

## Generate / enrollment secrets

`POST /api/v1/proxy-agent/generate/` (authenticated user, company from `request.user.cmp_id`):

| Issued once to agent | Stored server-side |
|----------------------|--------------------|
| Client private key | Never stored |
| Raw bootstrap token | AES-GCM only |
| Raw `progress_signing_secret` | Hash + AES-GCM |
| Client cert PEM | AES-GCM copy |
| `trusted_ca_cert` | Public CA PEM |

Requires `PROXY_AGENT_CA_CERT_PATH` / `PROXY_AGENT_CA_KEY_PATH` (batch: mounted from Secret `proxy-agent-ca`).

## Batch deploy checklist (security-related)

1. Secrets: `proxy-agent-ca` (cert+key), `proxy-agent-client-ca` (cert for ingress).
2. Django volume mount `/etc/proxy-agent-ca` + env paths.
3. Deployment `proxy-agent-gateway` + Service; NetworkPolicy gateway → django :8000.
4. Ingress `agent-api-ingress` with `auth-tls-verify-client: on`.
5. DNS A record + cert-manager TLS (`agent-api-tls`).
6. Never set `PROXY_AGENT_ALLOW_HMAC_DEV` in production.

## gRPC primary note

Hub implements `ReportProgress` and relays to the same Django ingest (`X-INTERNAL-KEY` + `X-Agent-UID` from cert). Do not expose Django on the public gRPC port. HTTPS gateway remains deployed for fallback and sites that temporarily lack hub progress RPC.

## Related

- [service-auth.md](./service-auth.md) — internal key contract
- [kubernetes.md](./kubernetes.md) — pod / NetworkPolicy hardening
- [field-encryption.md](./field-encryption.md) — AES-GCM field crypto
- Ops: [proxy-agent gateway](../../syncdatesy-adapter/docs/operations/proxy-agent/GATEWAY.md)
- DNS steps: [DNS and TLS](../../syncdatesy-adapter/docs/operations/proxy-agent/DNS-AND-TLS.md)
